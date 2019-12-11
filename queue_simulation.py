import simpy, random


# Globals
NUM_HELPSEEKERS_IN_SYSTEM = 0
NUM_HELPSEEKERS_IN_QUEUE = 0
NUM_HELPSEEKERS_IN_PRIORITY_QUEUE = [0, 0, 0]
MEAN_QUEUE_TIME_BEFORE_RENEGING = 7.0 # specify this as a float
LAMBDA_RENEGE = 1.0/MEAN_QUEUE_TIME_BEFORE_RENEGING # mean reneging counts




class Counsellor:
    '''
        Counsellor Class with a limited number of counsellors to serve helpseekers
    '''

    def __init__(self, env, counsellor_id, service_duration, lunch_duration):
        self.env = env
        self.state = env.process(self.work() )
        self.counsellor_id = counsellor_id
        self.service_duration = service_duration
        self.lunch_duration = lunch_duration

    #---------------------------------------------------------------------------
    
    def work(self):
        while True:
            
            # start counselling
            print(f'Counsellor {self.counsellor_id} starts working at {self.env.now}')
            yield self.env.timeout(self.service_duration)


            # get lunch break
            print(f'Counsellor {self.counsellor_id} takes a lunch break')
            self.env.process(self.lunch() )

            # print(f'Counsellor {self.counsellor_id} takes a lunch break')
            # try:
            #     yield self.env.process(self.lunch() )
            # except simpy.Interrupt:
            #     # stop eating lunch 
            #     print(f'Too many people waiting.'
            #         f' Duty Officer summons counsellor'
            #         f' {self.counsellor_id} back to work')

    
    #---------------------------------------------------------------------------

    def lunch(self):
        '''
            Lunch Break
        '''
        yield self.env.timeout(self.lunch_duration)


#-------------------------------------------------------end of Counsellors class

class Helpseeker:
    '''
        Helpseeker Class to be created according to an exponential distribution
    '''

    def __init(self, env, helpseeker_id, openup_service, chat_duration):
        self.env = env
        self.chat_duration = chat_duration
        self.openup_service = openup_service
        self.helpseeker_id = f'Helpseeker {helpseeker_id}'


    def accept_TOS(self):
        # helpseeker patience follows an exponential distribution
        patience = random.expovariate(LAMBDA_RENEGE)

        print(f'{self.helpseeker_id} has accepted TOS.  Chat session created')

        with openup_service.request() as req:
            yield req

            print(f'{self.helpseeker_id} now being served')
            yield self.env.timeout(self.chat_duration)
            print(f'{self.helpseeker_id} chat session terminates')

    def renege(self):




def main():
    env = simpy.Environment()
    counsellor = Counsellor(env, 1, 8, 1)
    env.run(until=20)


if __name__ == '__main__':
    main()






# class QueueSimulationModel:
#     '''
#         The Queue Simulation Model
#     '''

#     def __init(self):
#         self.env = sp.Environment() # the execution environment
